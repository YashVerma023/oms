"""Data Operation: creating a table by hand and from a sheet.

    python tests/test_data_ops.py

The identifier checks come first: this is the only place in the portal that
puts user input into a SQL identifier, so it gets tested like a trust boundary.
"""

from __future__ import annotations

import io
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

from core import data_ops  # noqa: E402
from database.db import db  # noqa: E402

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
        print(f"FAIL  {label} -> accepted, should have been rejected")
    except data_ops.DataOpError as exc:
        print(f"PASS  {label} -> rejected: {str(exc)[:60]}")


def csv_file(text_body: str) -> io.BytesIO:
    return io.BytesIO(text_body.encode("utf-8"))


SAMPLE = (
    "User ID,Alias,Allocation,Started,Active\r\n"
    "AB123,MSR_ONE_2C,200000,2026-08-21,True\r\n"
    "CD456,MSR_TWO_5C,500000,2026-08-20,False\r\n"
    "EF789,MSR_THREE_1C,100000,2026-08-19,True\r\n"
)


def make_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    return app


def run() -> None:
    print("-- identifiers: the only user input that reaches SQL structure --")
    check("a plain name passes", data_ops.check_identifier("mtm_history", "Table"),
          "mtm_history")
    for bad in ["drop table x", "a-b", "a;b", "`x`", "1st", "", "  ",
                "x' OR '1'='1", "a b", "tab\tname", "über"]:
        rejects(f"rejects {bad!r}", lambda b=bad: data_ops.check_identifier(b, "Table"))
    rejects("rejects an over-long name",
            lambda: data_ops.check_identifier("x" * 65, "Table"))

    print("\n-- types come from the allow-list, never from the request --")
    rejects("unknown type",
            lambda: data_ops._clean_columns([{"name": "a", "type": "BLOB; DROP"}]))
    rejects("duplicate column",
            lambda: data_ops._clean_columns(
                [{"name": "a", "type": "text"}, {"name": "A", "type": "text"}]))
    rejects("no columns", lambda: data_ops._clean_columns([]))
    # TEXT cannot be indexed without a prefix length, so a key column downgrades.
    check("a key column of long text becomes text",
          data_ops._clean_columns(
              [{"name": "a", "type": "longtext", "key": True}])[0]["type"],
          "text")

    print("\n-- guessing types from values --")
    check("integers", data_ops._looks_like(["1", "2", "30"]), "int")
    check("decimals", data_ops._looks_like(["1.5", "2", ""]), "decimal")
    check("booleans", data_ops._looks_like(["True", "False", "yes"]), "bool")
    check("dates", data_ops._looks_like(["2026-08-21", "2026-01-02"]), "date")
    check("text", data_ops._looks_like(["AB123", "7"]), "text")
    # _to_int truncates, so a lone int test would type these as BIGINT.
    check("a fraction is not a whole number",
          data_ops._looks_like(["1.5"]), "decimal")
    check("money keeps its paise",
          data_ops._looks_like(["200000.50", "300000"]), "decimal")
    check("all blank falls back to text", data_ops._looks_like([None, "", "  "]), "text")

    print("\n-- names taken from sheet headers --")
    taken: set[str] = set()
    check("spaces become underscores",
          data_ops._column_name("User ID", 0, taken), "User_ID")
    check("a repeat is made unique",
          data_ops._column_name("User ID", 1, taken), "User_ID_2")
    check("a blank header still gets a name",
          data_ops._column_name("", 2, taken), "column_3")
    check("a leading digit is fixed",
          data_ops._column_name("2026 total", 3, taken), "col_2026_total")

    app = make_app()
    with app.app_context():
        # SQLite has no storage engines and no information_schema.
        data_ops.TABLE_OPTIONS = ""
        # The app checks MySQL's catalogue; here we check SQLite's.
        data_ops.table_exists = lambda name: bool(db.session.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name}).scalar())

        print("\n-- reading a sheet --")
        result = data_ops.inspect(csv_file(SAMPLE), "positions.csv")
        check("column count", len(result["columns"]), 5)
        check("data rows counted", result["rows"], 3)
        check("suggested names",
              [c["name"] for c in result["columns"]],
              ["User_ID", "Alias", "Allocation", "Started", "Active"])
        check("suggested types",
              [c["type"] for c in result["columns"]],
              ["text", "text", "int", "date", "bool"])
        check("samples shown", result["columns"][0]["samples"][:2], ["AB123", "CD456"])

        print("\n-- creating from that sheet, with a two-column key --")
        columns = [
            {"name": "user_id", "type": "text", "key": True},
            {"name": "alias", "type": "text", "key": True},
            {"name": "allocation", "type": "int"},
            {"name": "started", "type": "date"},
            {"name": "active", "type": "bool"},
        ]
        created = data_ops.create_table(
            "broker_positions", columns, csv_file(SAMPLE), "positions.csv")
        check("table name", created["table"], "broker_positions")
        check("composite key", created["keys"], ["user_id", "alias"])
        check("rows loaded", created["loaded"], 3)
        check("nothing skipped", created["skipped"], 0)
        check("key columns are NOT NULL",
              created["ddl"].count("NOT NULL"), 2)

        stored = db.session.execute(text(
            "SELECT user_id, allocation, started, active FROM broker_positions "
            "ORDER BY user_id")).all()
        check("values converted, not stored as text",
              [tuple(r) for r in stored][0][:2], ("AB123", 200000))

        print("\n-- guards --")
        rejects("the same table twice", lambda: data_ops.create_table(
            "broker_positions", columns))
        rejects("a portal table", lambda: data_ops.create_table(
            "all_users", columns))
        rejects("a table named to inject", lambda: data_ops.create_table(
            "x`; DROP TABLE all_users; --", columns))

        print("\n-- leaving a column out must not shift the others --")
        # Take columns 0, 1 and 4 of the sheet, skipping Allocation and Started.
        subset = [
            {"name": "user_id", "type": "text", "key": True, "index": 0},
            {"name": "alias", "type": "text", "index": 1},
            {"name": "active", "type": "bool", "index": 4},
        ]
        created = data_ops.create_table(
            "subset_test", subset, csv_file(SAMPLE), "positions.csv")
        check("only the chosen columns exist", created["columns"], 3)
        check("all rows still load", created["loaded"], 3)
        rows = db.session.execute(text(
            "SELECT user_id, alias, active FROM subset_test ORDER BY user_id")).all()
        check("values came from the right sheet columns",
              [tuple(r) for r in rows],
              [("AB123", "MSR_ONE_2C", 1), ("CD456", "MSR_TWO_5C", 0),
               ("EF789", "MSR_THREE_1C", 1)])

        print("\n-- a sheet with a duplicate key value --")
        dupe = SAMPLE + "AB123,MSR_ONE_2C,999999,2026-08-18,True\r\n"
        created = data_ops.create_table(
            "dupe_test", columns, csv_file(dupe), "positions.csv")
        check("the repeat is skipped, not fatal", created["skipped"], 1)
        check("the rest still load", created["loaded"], 3)


if __name__ == "__main__":
    run()
    print("\n" + ("All Data Operation checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
