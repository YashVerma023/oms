"""Usersetting is keyed on User ID + server.

    python tests/test_usersetting_key.py

Reproduces the failure from the log first - a shared account (FEED / ZG0636)
present in two servers' files - then proves the composite key fixes it without
letting real duplicates through.
"""

from __future__ import annotations

import csv
import dataclasses
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

from core import importer  # noqa: E402
from database import schema  # noqa: E402
from database.db import db  # noqa: E402

SOURCE = ROOT / "data" / "USERSETTING.csv"

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got}   want {want}")


def source_rows() -> tuple[list[str], list[list[str]]]:
    with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[:7], rows[7:]


def build_file(preamble, header, rows) -> io.BytesIO:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    for line in preamble:
        writer.writerow(line)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return io.BytesIO(out.getvalue().encode("utf-8"))


def make_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    return app


def create_table(columns: list[str]) -> None:
    body = ", ".join(f"`{c}` TEXT" for c in columns)
    db.session.execute(text(
        f"CREATE TABLE usersetting ({body}, `server` TEXT NOT NULL DEFAULT '', "
        f"`algo` TEXT, PRIMARY KEY (`User ID`, `server`))"))
    db.session.commit()


def run() -> None:
    print("-- the key declared in the schema --")
    check("primary key", schema.ddl_primary_key("usersetting"), ["User ID", "server"])
    check("import spec key", list(importer.IMPORT_SPECS["usersetting"].pk),
          ["User ID", "server"])

    preamble, data = source_rows()
    header = preamble[6]
    preamble = preamble[:6]

    # FEED is the account that broke the upload: it sits in every server's file.
    shared = [r for r in data if r and r[2].strip().upper() == "ZG0636"]
    check("the shared account is in the sample file", len(shared), 1)

    feed_row = shared[0]
    others = [r for r in data if r and r[2].strip().upper() != "ZG0636"]
    first = [feed_row] + others[:5]        # FEED plus five accounts
    second = [feed_row] + others[5:10]     # FEED again, five different accounts
    check("FEED is in both files",
          all(any(r[2] == "ZG0636" for r in batch) for batch in (first, second)),
          True)

    app = make_app()
    with app.app_context():
        columns = [c for c, _ in schema.ddl_columns("usersetting")
                   if c not in ("server", "algo", "created_at", "updated_at")]
        create_table(columns)

        # information_schema does not exist on SQLite, and algo is derived from
        # a table this test does not build.
        importer._target_columns = lambda table: [
            {"name": c, "data_type": "varchar", "is_bool": False,
             "length": 255, "precision": 0, "scale": 0}
            for c in columns + ["server"]
        ]
        # post_write copies algo from all_users, a table this test does not
        # build. The spec captured the function at import time, so replace it.
        importer.IMPORT_SPECS["usersetting"] = dataclasses.replace(
            importer.IMPORT_SPECS["usersetting"], post_write=None)

        print("\n-- both servers load, and FEED survives on each --")
        report = importer.import_sheet("usersetting", [
            (build_file(preamble, header, first), "VS11 24 AUG 26 USERSETTINGS.csv"),
            (build_file(preamble, header, second), "VS30 24 AUG 26 USERSETTINGS.csv"),
        ])
        check("rows loaded", report.loaded, len(first) + len(second))
        check("nothing skipped as duplicate", report.skipped, 0)

        feed = db.session.execute(text(
            "SELECT `server` FROM usersetting WHERE `User ID`='ZG0636' "
            "ORDER BY `server`")).scalars().all()
        check("FEED exists once per server", feed, ["VS11", "VS30"])

        print("\n-- re-uploading one server replaces only that server --")
        report = importer.import_sheet("usersetting", [
            (build_file(preamble, header, first), "VS11 25 AUG 26 USERSETTINGS.csv"),
        ])
        check("second upload succeeds", report.loaded, len(first))
        feed = db.session.execute(text(
            "SELECT `server` FROM usersetting WHERE `User ID`='ZG0636' "
            "ORDER BY `server`")).scalars().all()
        check("FEED still on both servers", feed, ["VS11", "VS30"])
        total = db.session.execute(text("SELECT COUNT(*) FROM usersetting")).scalar()
        check("no rows duplicated", total, len(first) + len(second))

        print("\n-- a real duplicate inside one file is still dropped --")
        report = importer.import_sheet("usersetting", [
            (build_file(preamble, header, first + [first[0]]),
             "VS11 26 AUG 26 USERSETTINGS.csv"),
        ])
        check("the repeat is skipped", report.skipped, 1)
        check("only the unique rows load", report.loaded, len(first))


if __name__ == "__main__":
    run()
    print("\n" + ("All usersetting key checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
