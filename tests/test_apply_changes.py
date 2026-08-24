"""Writing the allocation check back: who gets written, and who must not.

    python tests/test_apply_changes.py
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

sqlite3.register_adapter(Decimal, float)

from core import setup_check  # noqa: E402
from database.db import db  # noqa: E402

DAY = dt.date(2026, 8, 21)

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got!r}   want {want!r}")


def seed() -> None:
    db.session.execute(text("""
        CREATE TABLE all_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, `userId` TEXT,
            `allocation` REAL, `server` TEXT, `Date` DATE)"""))
    db.session.execute(text("""
        CREATE TABLE usersetting (
            `User ID` TEXT, `Remarks` TEXT, `server` TEXT, `User Alias` TEXT,
            PRIMARY KEY (`User ID`, `server`))"""))

    for uid, server in (("LIVE1", "VS1"), ("LIVE2", "VS2")):
        db.session.execute(
            text("INSERT INTO all_users (`userId`,`allocation`,`server`,`Date`)"
                 " VALUES (:u, 0, :s, :d)"),
            dict(u=uid, s=server, d=DAY),
        )

    for uid, server, alias in (
        ("LIVE1", "VS1", "MSR_LIVE1_POS"),
        ("LIVE2", "VS2", "MSR_LIVE2_POS"),
        # Same User ID as a live account, on the feed server. Never written.
        ("LIVE1", "VS8", "VS8_FEED"),
        ("LIVE2", "VS9", "MSP_FEED_02"),
        # A row with no alias at all is still a real account.
        ("LIVE2", "VS3", None),
    ):
        db.session.execute(
            text("INSERT INTO usersetting VALUES (:u, '', :s, :a)"),
            dict(u=uid, s=server, a=alias),
        )
    db.session.commit()


def remarks() -> dict[tuple[str, str], str]:
    return {
        (row[0], row[1]): row[2]
        for row in db.session.execute(
            text("SELECT `User ID`, `server`, `Remarks` FROM usersetting")
        ).all()
    }


def run() -> None:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)

    with app.app_context():
        seed()

        result = setup_check.apply_changes(DAY, [
            {"userid": "LIVE1", "expected": 250000},
            {"userid": "LIVE2", "expected": 300000},
        ])

        stored = remarks()
        print("-- the live rows are written --")
        check("LIVE1 on its own server", stored[("LIVE1", "VS1")], "250000")
        check("LIVE2 on its own server", stored[("LIVE2", "VS2")], "300000")
        check("a row with no alias is still written",
              stored[("LIVE2", "VS3")], "300000")

        print("\n-- feed logins are skipped --")
        check("VS8_FEED untouched", stored[("LIVE1", "VS8")], "")
        check("MSP_FEED_02 untouched", stored[("LIVE2", "VS9")], "")
        check("and not counted as written", result["remarks"], 3)

        print("\n-- allocations still land on all_users --")
        allocations = dict(db.session.execute(
            text("SELECT `userId`, `allocation` FROM all_users")).all())
        check("LIVE1", allocations["LIVE1"], 250000.0)
        check("LIVE2", allocations["LIVE2"], 300000.0)

        print("\n-- commit=False leaves the transaction open --")
        setup_check.apply_changes(
            DAY, [{"userid": "LIVE1", "expected": 999}], commit=False
        )
        db.session.rollback()
        check("the rolled-back write is gone",
              remarks()[("LIVE1", "VS1")], "250000")


if __name__ == "__main__":
    run()
    print("\n" + ("All apply checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
