"""Per-server usersetting download.

    python tests/test_usersetting_export.py

The point of the format is that a downloaded file can go straight back into the
trading platform, so the test loads data/USERSETTING.csv, exports it again, and
compares the two.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import sys
import types
import zipfile
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

from core import usersetting_export as export  # noqa: E402
from database.db import db  # noqa: E402

SOURCE = ROOT / "data" / "USERSETTING.csv"

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        shown = str(got)
        print(f"PASS  {label} -> {shown[:70]}")
    else:
        failures.append(label)
        print(f"FAIL  {label}\n      got  {str(got)[:120]}\n      want {str(want)[:120]}")


def build_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    return app


def create_table() -> None:
    columns = ", ".join(f"`{c}` TEXT" for c in export.export_columns())
    db.session.execute(text(f"CREATE TABLE usersetting ({columns}, `server` TEXT, `algo` TEXT)"))


def load_source() -> list[list[str]]:
    with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def seed_from_source(servers: dict[str, str]) -> list[list[str]]:
    """Insert data/USERSETTING.csv, assigning each row a server."""
    rows = load_source()
    header, data = rows[6], rows[7:]
    columns = export.export_columns()
    check("the file's header round-trips exactly", export.header_row(), header)
    check("stripped, it matches the table columns",
          [h.strip() for h in header], columns)

    placeholders = ", ".join(f":c{i}" for i in range(len(columns)))
    quoted = ", ".join(f"`{c}`" for c in columns)
    for index, row in enumerate(data):
        params = {f"c{i}": (row[i] if i < len(row) else "") for i in range(len(columns))}
        params["srv"] = servers.get(row[2], "VS3")
        db.session.execute(
            text(f"INSERT INTO usersetting ({quoted}, `server`) "
                 f"VALUES ({placeholders}, :srv)"),
            params,
        )
    db.session.commit()
    return data


def run() -> None:
    app = build_app()
    on_date = dt.date(2026, 8, 21)

    with app.app_context():
        create_table()
        # Two servers, so grouping and the archive both get exercised.
        source = load_source()[7:]
        assignment = {row[2]: ("VS7" if n % 3 == 0 else "VS3")
                      for n, row in enumerate(source)}
        data = seed_from_source(assignment)

        print("\n-- file names --")
        check("name format", export.filename("vs3", on_date), "VS3 21 AUG 26 USERSETTINGS.csv")
        check("archive name", export.archive_name(on_date), "USERSETTINGS 21 AUG 26.zip")

        print("\n-- structure --")
        files, orphans = export.build(None, on_date)
        check("one file per server", sorted(n for n, _ in files),
              ["VS3 21 AUG 26 USERSETTINGS.csv", "VS7 21 AUG 26 USERSETTINGS.csv"])
        check("nothing skipped", orphans, [])

        by_name = dict(files)
        parsed = list(csv.reader(io.StringIO(by_name["VS3 21 AUG 26 USERSETTINGS.csv"])))
        check("six preamble lines then the header",
              [r[0] for r in parsed[:6]] == [p.split(",")[0] for p in export.PREAMBLE],
              True)
        check("header row is row 7", parsed[6], export.header_row())
        check("37 columns", len(parsed[6]), 37)
        check("Remarks is last", parsed[6][-1], "Remarks")
        check("CRLF line endings",
              by_name["VS3 21 AUG 26 USERSETTINGS.csv"].startswith(export.PREAMBLE[0] + "\r\n"),
              True)

        print("\n-- every account is present, exactly once --")
        exported = {}
        for name, body in files:
            for row in list(csv.reader(io.StringIO(body)))[7:]:
                if row:
                    exported[row[2]] = row
        check("account count", len(exported), len(data))
        check("no account lost", sorted(exported), sorted(r[2] for r in data))

        print("\n-- values survive the round trip --")
        original = {row[2]: row for row in data}
        mismatched = []
        for uid, row in exported.items():
            for i, (was, now) in enumerate(zip(original[uid], row)):
                if was != now:
                    mismatched.append((uid, export.export_columns()[i], was, now))
        check("cells identical to the uploaded file", mismatched[:5], [])

        print("\n-- it can be re-uploaded: the name passes the import check --")
        from core.importer import server_from_filename
        for name, _ in files:
            check(f"'{name}' parses", server_from_filename(name),
                  {"server": name.split()[0]})

        print("\n-- the archive --")
        blob = export.zipped(files)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            check("archive holds both files", sorted(archive.namelist()),
                  ["VS3 21 AUG 26 USERSETTINGS.csv", "VS7 21 AUG 26 USERSETTINGS.csv"])

        print("\n-- operator scoping --")
        files, _ = export.build(["VS7"], on_date)
        check("one server only", [n for n, _ in files], ["VS7 21 AUG 26 USERSETTINGS.csv"])
        check("no servers assigned -> nothing", export.build([], on_date), ([], []))

        print("\n-- rows with no server --")
        db.session.execute(text(
            "INSERT INTO usersetting (`User ID`, `server`) VALUES ('NOSERVER', '')"))
        db.session.commit()
        files, orphans = export.build(None, on_date)
        check("reported, not exported", orphans, ["NOSERVER"])
        check("still two files", len(files), 2)


if __name__ == "__main__":
    run()
    print("\n" + ("All usersetting export checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
